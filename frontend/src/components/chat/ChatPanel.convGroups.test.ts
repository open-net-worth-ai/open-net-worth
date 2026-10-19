// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { fDateTime } from '../../utils/format'
import { conversationGroups, conversationWhen } from './ChatPanel'
import type { ConversationMeta } from '../../hooks/useConversations'

// Every run of one Automation carries ITS name, so twenty of them interleaved with real
// chats read as twenty duplicates of the same thread — and "4m ago" on all of them says
// nothing about which is which.

const conv = (over: Partial<ConversationMeta>): ConversationMeta => ({
  id: 'c1',
  title: 'Log trade & transactions from emails',
  createdAt: '2026-08-03T03:08:00Z',
  updatedAt: '2026-08-03T03:08:00Z',
  messageCount: 4,
  model: '',
  ...over,
})

describe('conversationGroups', () => {
  it('separates Automation runs from chats', () => {
    const groups = conversationGroups([
      conv({ id: 'a', source: 'automation' }),
      conv({ id: 'b', title: 'What did I spend on food?' }),
      conv({ id: 'c', source: 'automation' }),
    ])

    expect(groups.map((g) => [g.key, g.items.length])).toEqual([
      ['chat', 1],
      ['automation', 2],
    ])
  })

  it('lists captures with the chats', () => {
    const rows = [
      conv({ id: 'chat', title: 'What did I spend on food?' }),
      conv({ id: 'cap', title: 'Capture · Citi: $12.40 at Blue Bottle', origin: 'capture' }),
    ]
    const ids = conversationGroups(rows)
      .find((g) => g.key === 'chat')!
      .items.map((c) => c.id)
      .sort()

    expect(ids).toEqual(['cap', 'chat'])
  })

  it('puts chats first — they are the ones you came here to switch between', () => {
    const groups = conversationGroups([conv({ id: 'a', source: 'automation' }), conv({ id: 'b' })])

    expect(groups[0]!.key).toBe('chat')
  })

  it('never hides a run — every one is a conversation you can reopen', () => {
    const many = Array.from({ length: 25 }, (_, i) => conv({ id: `r${i}`, source: 'automation' }))
    const groups = conversationGroups([...many, conv({ id: 'chat', title: 'A real chat' })])
    const runs = groups.find((g) => g.key === 'automation')!

    expect([runs.items.length, runs.total]).toEqual([25, 25])
  })

  it('lists runs newest first, by when the run HAPPENED', () => {
    // Every Automation session on this instance carries the same updated_at to the
    // millisecond — a maintenance pass touched them in one batch — so sorting by it put
    // the 7:10am run below the 2:32am one. created_at is when the run started.
    const stamped = '2026-08-02T23:20:06.700Z'
    const runs = conversationGroups([
      conv({
        id: 'early',
        source: 'automation',
        createdAt: '2026-08-03T02:32:00Z',
        updatedAt: stamped,
      }),
      conv({
        id: 'late',
        source: 'automation',
        createdAt: '2026-08-03T07:10:00Z',
        updatedAt: stamped,
      }),
    ])[0]!

    expect(runs.items.map((c) => c.id)).toEqual(['late', 'early'])
  })

  it('never hides a chat, however many there are', () => {
    const chats = Array.from({ length: 40 }, (_, i) => conv({ id: `c${i}` }))

    expect(conversationGroups(chats)[0]!.items).toHaveLength(40)
  })

  it('omits a group with nothing in it rather than showing an empty header', () => {
    expect(conversationGroups([conv({ id: 'b' })]).map((g) => g.key)).toEqual(['chat'])
    expect(conversationGroups([]).length).toBe(0)
  })

  // Was: "treats an unknown source as a chat". That is the behaviour this file now
  // forbids. `source` names the conversation's OWNING SURFACE — a browser chat has none —
  // so defining chats as "not automation" swept every other surface into the web sidebar.
  // Live 2026-08-21: the owner's Telegram thread with Lucius sat among their browser chats,
  // titled with a machine-written proactive marker, and they reasonably asked why a phone
  // conversation was in the web app. It was, and this test had blessed it.
  it('keeps a conversation arriving over a TRANSPORT out of the in-app chat list', () => {
    expect(conversationGroups([conv({ channel: 'telegram' })])).toEqual([])
  })

  // In-app is the ABSENCE of both axes — no runner owns it, no transport carries it. It
  // deliberately has no value of its own: naming it 'web' would be wrong the moment the same
  // conversation is opened from the desktop or mobile app, which are not different places.
  it('lists a conversation with neither a source nor a channel as an in-app chat', () => {
    expect(conversationGroups([conv({ source: undefined })])[0]!.key).toBe('chat')
  })
})

describe('conversationWhen', () => {
  it('gives a run the clock time — down a column of runs that IS the identity', () => {
    const when = conversationWhen(conv({ source: 'automation' }))

    expect(when).toMatch(/\d/)
    expect(when).not.toContain('ago')
  })

  it('stamps a chat with its LAST MESSAGE, in the same clock form as a run', () => {
    // One column, one kind of fact — "1h ago" beside "Aug 5, 11:00 PM" read as two.
    const hourAgo = new Date(Date.now() - 3_600_000)
    const when = conversationWhen(
      conv({ createdAt: '2026-08-01T03:08:00Z', updatedAt: hourAgo.toISOString() }),
    )

    expect(when).not.toContain('ago')
    // The stamp is the last message, NOT the day the conversation was started.
    expect(when).toBe(fDateTime(hourAgo))
  })

  it('falls back to the OTHER timestamp before giving up on the clock', () => {
    // A chat whose updated_at is unusable still has a created_at — a clock stamp from that
    // beats "Invalid Date", and beats dropping the row back to a relative time on its own.
    const when = conversationWhen(conv({ createdAt: '2026-08-01T03:08:00Z', updatedAt: 'nope' }))

    expect(when).not.toContain('Invalid')
    expect(when).toBe(fDateTime(new Date('2026-08-01T03:08:00Z')))
  })

  it("uses a run's updated_at when its created_at is unusable", () => {
    const hourAgo = new Date(Date.now() - 3_600_000)
    const when = conversationWhen(
      conv({ source: 'automation', createdAt: 'not-a-date', updatedAt: hourAgo.toISOString() }),
    )

    expect(when).toBe(fDateTime(hourAgo))
  })

  it('renders NO stamp when neither timestamp parses — never "Invalid Date"', () => {
    const when = conversationWhen(conv({ createdAt: 'not-a-date', updatedAt: 'not-a-date' }))

    expect(when).toBe('')
  })
})

// @vitest-environment jsdom
//
// Live report 2026-11-04: an approved Automation run's answer showed as "t, so the notification
// did not reach you", 44 characters short, exactly the text the bubble held from before its card.
// A reconnect during the resumed turn counted that text as replayable, but a replay only covers
// the running turn, and a card ends the turn before it.

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { consumeEventStream } from './chatReducer'
import { chatSessions } from './chatSessions'
import type { Message, ProposedChange } from '../types/chat'

vi.mock('../utils/chatStore', () => ({ saveMessages: vi.fn() }))
vi.mock('../api/client', () => ({ authFetch: vi.fn(), setUnauthorizedHandler: vi.fn() }))

function sseResponse(events: object[]): Response {
  const body =
    events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('') + 'data: [DONE]\n\n'
  return new Response(
    new ReadableStream({
      start(ctrl) {
        ctrl.enqueue(new TextEncoder().encode(body))
        ctrl.close()
      },
    }),
    { status: 200 },
  )
}

const BEFORE_CARD = 'Logged three rows. '
const BEFORE_THINKING = 'Those rows look right.'
const REPLAYED = 'Sending the alert. '
const REPLAYED_THINKING = 'The card was approved.'
const FRESH = 'The channel is not set up, so the notification did not reach you.'

function card(status?: ProposedChange['status']): ProposedChange {
  return { id: 'tu-card', type: 'tool_permission', grant_tool: 'set_payees_rules', status }
}

/** A bubble paused on a card, its card answered, partway through the resumed turn. */
function resumedBubble(status: ProposedChange['status'] = 'confirmed'): Message {
  return {
    id: 'a1',
    role: 'assistant',
    text: BEFORE_CARD + REPLAYED,
    thinking: BEFORE_THINKING + REPLAYED_THINKING,
    streaming: true,
    proposedChanges: [card(status)],
    blocks: [
      { type: 'thinking', content: BEFORE_THINKING },
      { type: 'text', content: BEFORE_CARD },
      { type: 'change', change: card(status) },
      { type: 'thinking', content: REPLAYED_THINKING },
      { type: 'text', content: REPLAYED },
    ],
  }
}

async function reconnect(conv: string, seeded: Message) {
  chatSessions.apply(conv, (s) => ({ ...s, status: 'streaming', messages: [seeded] }))
  await consumeEventStream(
    conv,
    sseResponse([
      { type: 'thinking_delta', text: REPLAYED_THINKING },
      { type: 'text_delta', text: REPLAYED },
      { type: 'text_delta', text: FRESH },
      { type: 'turn_done', usage: {} },
    ]),
    { suggestionsEnabled: false },
  )
  return chatSessions.getSlice(conv).messages.at(-1)
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('a reconnect during a turn resumed from a card', () => {
  it('skips only what this turn already showed, never the text from before the card', async () => {
    const message = await reconnect('conv-card-resume', resumedBubble())
    expect(message?.text).toBe(BEFORE_CARD + REPLAYED + FRESH)
    expect(message?.thinking).toBe(BEFORE_THINKING + REPLAYED_THINKING)
  })

  it('holds for a denied card too, since that resumes the turn the same way', async () => {
    const message = await reconnect('conv-card-denied', resumedBubble('dismissed'))
    expect(message?.text).toBe(BEFORE_CARD + REPLAYED + FRESH)
  })

  it('still counts the whole bubble while its card is unanswered: nothing has paused yet', async () => {
    const pending: Message = {
      id: 'a1',
      role: 'assistant',
      text: REPLAYED,
      streaming: true,
      proposedChanges: [card()],
      blocks: [
        { type: 'text', content: REPLAYED },
        { type: 'change', change: card() },
      ],
    }
    chatSessions.apply('conv-card-pending', (s) => ({
      ...s,
      status: 'streaming',
      messages: [pending],
    }))
    await consumeEventStream(
      'conv-card-pending',
      sseResponse([
        { type: 'text_delta', text: REPLAYED },
        { type: 'text_delta', text: FRESH },
        { type: 'turn_done', usage: {} },
      ]),
      { suggestionsEnabled: false },
    )
    expect(chatSessions.getSlice('conv-card-pending').messages.at(-1)?.text).toBe(REPLAYED + FRESH)
  })
})

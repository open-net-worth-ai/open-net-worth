// @vitest-environment jsdom
//
// Live call, 2026-11-03: Lucius's spoken answers showed in the chat missing their first words
// ("se twenty billion dollars…"), and exactly as many characters as the small-talk reply just
// before them. That reply is recorded with no usage, so the attach took it for an interrupted
// bubble and armed the replay skip off it — then skipped that many characters of the NEXT
// turn's answer. A new user message in the stream means a new exchange: nothing is skipped.

import { describe, it, expect, vi } from 'vitest'
import { consumeEventStream } from './chatReducer'
import { chatSessions } from './chatSessions'
import type { Message } from '../types/chat'

vi.mock('../utils/chatStore', () => ({ saveMessages: vi.fn() }))
vi.mock('../api/client', () => ({ authFetch: vi.fn(), setUnauthorizedHandler: vi.fn() }))

function sseResponse(events: string[]): Response {
  const body = events.map((e) => `data: ${e}\n\n`).join('') + 'data: [DONE]\n\n'
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

describe('a new turn after a usage-less reply keeps its whole answer', () => {
  it('skips nothing once the stream announces a new user message', async () => {
    const smallTalk: Message = {
      id: 'a1',
      role: 'assistant',
      text: "I'm doing great, thanks for asking!",
      streaming: false,
      proposedChanges: [],
    }
    chatSessions.apply('conv-voice', (s) => ({ ...s, messages: [smallTalk] }))

    await consumeEventStream(
      'conv-voice',
      sseResponse([
        '{"type":"user_message","message_id":"voice_1","text":"How is ACME doing?","attachments":[]}',
        '{"type":"tool_use_start","id":"tu-9","name":"get_asset_context"}',
        '{"type":"tool_done","id":"tu-9","result":"ok"}',
        '{"type":"text_delta","text":"ACME fell about nine percent yesterday after it filed to raise money."}',
        '{"type":"turn_done","usage":{}}',
      ]),
      { suggestionsEnabled: false },
    )

    const msgs = chatSessions.getSlice('conv-voice').messages
    expect(msgs.map((m) => m.role)).toEqual(['assistant', 'user', 'assistant'])
    expect(msgs[0]?.text).toBe("I'm doing great, thanks for asking!")
    expect(msgs[2]?.text).toBe(
      'ACME fell about nine percent yesterday after it filed to raise money.',
    )
  })
})

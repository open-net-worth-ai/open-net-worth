// @vitest-environment jsdom
//
// A live `visual` SSE event appends the same `{type:'visual', visual}` block the reloaded
// transcript carries, in order with the text around it.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { consumeEventStream } from './chatReducer'
import { chatSessions } from './chatSessions'
import type { Message } from '../types/chat'

vi.mock('../utils/chatStore', () => ({ saveMessages: vi.fn() }))
vi.mock('../api/client', () => ({ authFetch: vi.fn(), setUnauthorizedHandler: vi.fn() }))

const VISUAL = {
  id: 'vis_000000000001',
  title: null,
  caption: [],
  blocks: [
    {
      type: 'stats',
      tiles: [{ label: 'This month', value: 4180, format: 'money', currency: 'SGD' }],
    },
  ],
}

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

beforeEach(() => vi.clearAllMocks())

describe('visual event appends a visual block', () => {
  it('keeps order with the text', async () => {
    const m: Message = {
      id: 'a1',
      role: 'assistant',
      text: '',
      streaming: true,
      toolCalls: [],
      blocks: [],
      proposedChanges: [],
    }
    chatSessions.apply('own-v', (s) => ({ ...s, messages: [m], openAssistantId: 'a1' }))
    await consumeEventStream(
      'own-v',
      sseResponse([
        JSON.stringify({ type: 'text_delta', text: 'Here it is. ' }),
        JSON.stringify({ type: 'visual', tool_use_id: 't1', visual: VISUAL }),
        JSON.stringify({ type: 'text_delta', text: 'Done.' }),
      ]),
      { suggestionsEnabled: false },
    )
    const blocks = chatSessions.getSlice('own-v').messages[0]!.blocks!
    expect(blocks.map((b) => b.type)).toEqual(['text', 'visual', 'text'])
    expect(blocks[1]).toEqual({ type: 'visual', visual: VISUAL })
  })
})

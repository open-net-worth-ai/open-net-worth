// @vitest-environment jsdom
//
// Probe for the reported bug: Lucius called run_screen_command
// (automations.review_draft), the backend parked it, the transcript carries the block —
// but no chip rendered live. This drives the reducer with the EXACT event the backend
// emits (backend/ai/assistant.py::_post_execute_events) to bisect emission vs reduction.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { consumeEventStream } from './chatReducer'
import { chatSessions } from './chatSessions'
import type { Message } from '../types/chat'

vi.mock('../utils/chatStore', () => ({ saveMessages: vi.fn() }))
vi.mock('../api/client', () => ({ authFetch: vi.fn(), setUnauthorizedHandler: vi.fn() }))

function assistantMsg(id: string): Message {
  return {
    id,
    role: 'assistant',
    text: '',
    streaming: true,
    toolCalls: [],
    blocks: [],
    proposedChanges: [],
  }
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

beforeEach(() => {
  vi.clearAllMocks()
})

describe('parked screen command -> chip block', () => {
  it('appends a screen_command block for the exact backend event shape', async () => {
    chatSessions.apply('conv-sc', (s) => ({
      ...s,
      messages: [assistantMsg('m1')],
      openAssistantId: 'm1',
    }))

    // Verbatim shape from _post_execute_events: type/tool_use_id/status/command/open.
    // `open` is null because the parked tool result carries no "open" key.
    const event = JSON.stringify({
      type: 'screen_command',
      tool_use_id: 'toolu_probe',
      status: 'parked',
      command: {
        name: 'automations.review_draft',
        args: { automation: { name: 'Monthly IRAS GIRO Tax Payment' } },
      },
      open: null,
    })

    await consumeEventStream('conv-sc', sseResponse([event]), { suggestionsEnabled: false })

    const blocks = chatSessions.getSlice('conv-sc').messages[0]?.blocks ?? []
    const chip = blocks.find((b) => b.type === 'screen_command')
    expect(chip).toBeDefined()
  })
})

describe('a connector setup that ran at once (Connectors already open)', () => {
  it('still gets its card, pointed at Connectors; other live commands do not', async () => {
    chatSessions.apply('conv-setup', (s) => ({
      ...s,
      messages: [assistantMsg('m1')],
      openAssistantId: 'm1',
    }))
    const setup = JSON.stringify({
      type: 'screen_command',
      tool_use_id: 'toolu_setup',
      status: 'dispatched',
      command: { name: 'connectors.open_setup', args: { id: 'mcp-webull' } },
      open: null,
    })
    const reset = JSON.stringify({
      type: 'screen_command',
      tool_use_id: 'toolu_reset',
      status: 'dispatched',
      command: { name: 'plan.reset', args: {} },
      open: null,
    })
    await consumeEventStream('conv-setup', sseResponse([setup, reset]), {
      suggestionsEnabled: false,
    })
    const blocks = chatSessions.getSlice('conv-setup').messages[0]?.blocks ?? []
    const cards = blocks.filter((b) => b.type === 'screen_command')
    expect(cards).toEqual([
      {
        type: 'screen_command',
        command: { name: 'connectors.open_setup', args: { id: 'mcp-webull' } },
        open: { route: 'connectors' },
      },
    ])
  })
})

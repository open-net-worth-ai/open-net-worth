// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { consumeEventStream } from './chatReducer'
import { chatSessions } from './chatSessions'

vi.mock('../utils/chatStore', () => ({ saveMessages: vi.fn() }))
vi.mock('../api/client', () => ({ authFetch: vi.fn(), setUnauthorizedHandler: vi.fn() }))

function sseResponse(events: string[]): Response {
  const encoder = new TextEncoder()
  const body = events.map((event) => `data: ${event}\n\n`).join('') + 'data: [DONE]\n\n'
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(body))
        controller.close()
      },
    }),
    { status: 200 },
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('skill lifecycle activity', () => {
  it('updates one loading block to loaded and preserves its activation source', async () => {
    chatSessions.apply('skill-activity', (slice) => ({
      ...slice,
      messages: [{ id: 'u1', role: 'user', text: '/account-balance-tracer' }],
    }))

    await consumeEventStream(
      'skill-activity',
      sseResponse([
        '{"type":"skill_loading","id":"skill:0:user:account-balance-tracer","skill_id":"account-balance-tracer","skill_name":"account-balance-tracer","source":"user"}',
        '{"type":"skill_loaded","id":"skill:0:user:account-balance-tracer","skill_id":"account-balance-tracer","skill_name":"account-balance-tracer","source":"user"}',
      ]),
      { suggestionsEnabled: false },
    )

    const assistant = chatSessions
      .getSlice('skill-activity')
      .messages.find((message) => message.role === 'assistant')
    expect(assistant?.blocks).toEqual([
      {
        type: 'skill',
        id: 'skill:0:user:account-balance-tracer',
        skillId: 'account-balance-tracer',
        name: 'account-balance-tracer',
        status: 'loaded',
        source: 'user',
      },
    ])
    expect('activeSkills' in chatSessions.getSlice('skill-activity')).toBe(false)
  })

  it('keeps distinct skills as distinct blocks and renders a failure', async () => {
    chatSessions.apply('many-skills', (slice) => ({ ...slice, messages: [] }))

    await consumeEventStream(
      'many-skills',
      sseResponse([
        '{"type":"skill_loading","id":"skill:first","skill_id":"first","skill_name":"first","source":"user"}',
        '{"type":"skill_loaded","id":"skill:first","skill_id":"first","skill_name":"first","source":"user"}',
        '{"type":"skill_loading","id":"skill:second","skill_id":"second","skill_name":"second","source":"model"}',
        '{"type":"skill_failed","id":"skill:second","skill_id":"second","skill_name":"second","source":"model","error":"Skill is disabled."}',
      ]),
      { suggestionsEnabled: false },
    )

    const blocks = chatSessions
      .getSlice('many-skills')
      .messages.find((message) => message.role === 'assistant')?.blocks
    expect(blocks).toEqual([
      {
        type: 'skill',
        id: 'skill:first',
        skillId: 'first',
        name: 'first',
        status: 'loaded',
        source: 'user',
      },
      {
        type: 'skill',
        id: 'skill:second',
        skillId: 'second',
        name: 'second',
        status: 'failed',
        source: 'model',
        error: 'Skill is disabled.',
      },
    ])
    expect('activeSkills' in chatSessions.getSlice('many-skills')).toBe(false)
  })

  it('uses a replayed skill id as the text-prefix anchor on reconnect', async () => {
    chatSessions.apply('skill-replay', (slice) => ({
      ...slice,
      openAssistantId: null,
      messages: [
        { id: 'u-replay', role: 'user', text: 'trace it' },
        {
          id: 'a-replay',
          role: 'assistant',
          text: 'Hello ',
          streaming: false,
          blocks: [
            {
              type: 'skill',
              id: 'skill:replay',
              skillId: 'trace',
              name: 'trace',
              status: 'loaded',
              source: 'model',
            },
            { type: 'text', content: 'Hello ' },
          ],
        },
        { id: 'u-queued-after-disconnect', role: 'user', text: 'queued follow-up' },
      ],
    }))

    await consumeEventStream(
      'skill-replay',
      sseResponse([
        '{"type":"skill_loading","id":"skill:replay","skill_id":"trace","skill_name":"trace","source":"model"}',
        '{"type":"skill_loaded","id":"skill:replay","skill_id":"trace","skill_name":"trace","source":"model"}',
        '{"type":"text_delta","text":"Hello world"}',
      ]),
      { suggestionsEnabled: false },
    )

    const messages = chatSessions.getSlice('skill-replay').messages
    const assistant = messages[1]
    expect(assistant?.text).toBe('Hello world')
    expect(
      assistant?.blocks?.filter((block) => block.type === 'skill').map((block) => block.id),
    ).toEqual(['skill:replay'])
    expect(messages.filter((message) => message.role === 'assistant')).toHaveLength(1)
  })

  it('turns an outstanding loading row into a failed row on terminal stream error', async () => {
    chatSessions.apply('skill-stream-error', (slice) => ({ ...slice, messages: [] }))

    await consumeEventStream(
      'skill-stream-error',
      sseResponse([
        '{"type":"skill_loading","id":"skill:error","skill_id":"trace","skill_name":"trace","source":"model"}',
        '{"type":"error","message":"Could not save the activation"}',
      ]),
      { suggestionsEnabled: false },
    )

    const block = chatSessions
      .getSlice('skill-stream-error')
      .messages.find((message) => message.role === 'assistant')
      ?.blocks?.find((candidate) => candidate.type === 'skill')
    expect(block).toMatchObject({
      type: 'skill',
      id: 'skill:error',
      status: 'failed',
      error: 'Could not save the activation',
    })
  })

  it('does not treat a fresh loaded transition as replay or truncate next-round text', async () => {
    chatSessions.apply('fresh-model-skill', (slice) => ({
      ...slice,
      openAssistantId: 'a-fresh',
      messages: [
        { id: 'u-fresh', role: 'user', text: 'trace this' },
        {
          id: 'a-fresh',
          role: 'assistant',
          text: 'I will check. ',
          streaming: true,
          blocks: [{ type: 'text', content: 'I will check. ' }],
        },
      ],
    }))

    await consumeEventStream(
      'fresh-model-skill',
      sseResponse([
        '{"type":"skill_loading","id":"skill:fresh","skill_id":"trace","skill_name":"trace","source":"model"}',
        '{"type":"skill_loaded","id":"skill:fresh","skill_id":"trace","skill_name":"trace","source":"model"}',
        '{"type":"text_delta","text":"The balance is correct."}',
      ]),
      // A deliberate continuation: a plain attach to a trailing open bubble is a reconnect and
      // arms the replay skip by design, which is not what this test is about.
      { suggestionsEnabled: false, isResume: true },
    )

    expect(chatSessions.getSlice('fresh-model-skill').messages[1]?.text).toBe(
      'I will check. The balance is correct.',
    )
  })
})

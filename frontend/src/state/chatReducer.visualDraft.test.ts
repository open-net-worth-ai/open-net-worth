// @vitest-environment jsdom
//
// A drawing Lucius is still writing streams in as `visual_draft` events: one draft block grows
// in place, the finished `visual` takes its place (so the drawing stays where it was drawn),
// and a draft whose call never finished is gone once the turn ends.

import { describe, it, expect, vi } from 'vitest'
import { consumeEventStream } from './chatReducer'
import { chatSessions } from './chatSessions'
import type { Message } from '../types/chat'

vi.mock('../utils/chatStore', () => ({ saveMessages: vi.fn() }))
vi.mock('../api/client', () => ({ authFetch: vi.fn(), setUnauthorizedHandler: vi.fn() }))

const open = (owner: string) =>
  chatSessions.apply(owner, (s) => ({
    ...s,
    messages: [
      {
        id: 'a1',
        role: 'assistant',
        text: '',
        streaming: true,
        toolCalls: [],
        blocks: [],
        proposedChanges: [],
      } as Message,
    ],
    openAssistantId: 'a1',
  }))

const stream = (events: object[]) => {
  const body = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('') + 'data: [DONE]\n\n'
  return new Response(
    new ReadableStream({
      start(c) {
        c.enqueue(new TextEncoder().encode(body))
        c.close()
      },
    }),
  )
}

const blocks = (owner: string) => chatSessions.getSlice(owner).messages[0]?.blocks ?? []

describe('visual_draft', () => {
  it('grows one draft in place, then the finished visual takes its place', async () => {
    open('vd-a')
    await consumeEventStream(
      'vd-a',
      stream([
        { type: 'text_delta', text: 'Here it is.' },
        { type: 'visual_draft', title: 'Spend', code: '<svg><rect' },
        { type: 'visual_draft', title: 'Spend', code: '<svg><rect/><text' },
      ]),
      { suggestionsEnabled: false },
    )
    expect(blocks('vd-a')).toEqual([
      { type: 'text', content: 'Here it is.' },
      {
        type: 'visual',
        draft: true,
        visual: { id: 'draft', title: 'Spend', code: '<svg><rect/><text' },
      },
    ])
    const done = { id: 'vis_1', title: 'Spend', code: '<svg><rect/><text/></svg>' }
    await consumeEventStream('vd-a', stream([{ type: 'visual', visual: done }]), {
      suggestionsEnabled: false,
    })
    expect(blocks('vd-a')[1]).toEqual({ type: 'visual', visual: done })
    expect(blocks('vd-a')).toHaveLength(2)
  })

  it('keeps two drawings in one reply apart, each finished in its own place', async () => {
    open('vd-c')
    await consumeEventStream(
      'vd-c',
      stream([
        { type: 'visual_draft', title: 'One', code: '<svg id="1">' },
        { type: 'tool_use_start', id: 't1', name: 'show_visual' },
        { type: 'visual_draft', title: 'Two', code: '<svg id="2">' },
        { type: 'tool_use_start', id: 't2', name: 'show_visual' },
        { type: 'visual', visual: { id: 'v1', title: 'One', code: '<svg id="1"/>' } },
        { type: 'visual', visual: { id: 'v2', title: 'Two', code: '<svg id="2"/>' } },
      ]),
      { suggestionsEnabled: false },
    )
    const visuals = blocks('vd-c').filter((b) => b.type === 'visual')
    expect(visuals.map((b) => (b.type === 'visual' ? [b.visual.id, !!b.draft] : null))).toEqual([
      ['v1', false],
      ['v2', false],
    ])
  })

  it('drops a draft whose call never finished when the turn ends', async () => {
    open('vd-b')
    await consumeEventStream(
      'vd-b',
      stream([{ type: 'visual_draft', title: null, code: '<svg>' }, { type: 'turn_done' }]),
      { suggestionsEnabled: false },
    )
    expect(blocks('vd-b').some((b) => b.type === 'visual')).toBe(false)
  })
})

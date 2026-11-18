// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { chatSessions } from './chatSessions'
import { _resetOutboxForTests, failQueued, noteSending, noteSent, settleOutbox } from './chatOutbox'
import type { Message } from '../types/chat'

const C = 'outbox-conv'
const payload = {
  text: 'x',
  attachments: [],
  effort: 'medium',
  model: '',
  mentions: [],
  respondingAgent: null,
  skills: [],
  screen: null,
}

function queue(...ids: string[]) {
  chatSessions.apply(C, (s) => ({
    ...s,
    queuedMessages: ids.map((id) => ({ id, role: 'user', text: id, pending: 'queued' }) as Message),
  }))
}
const outbox = () => chatSessions.getSlice(C).queuedMessages.map((m) => [m.id, m.pending])

afterEach(() => {
  _resetOutboxForTests()
  chatSessions.apply(C, (s) => ({ ...s, queuedMessages: [] }))
})

describe('the outbox never leaves a message "queued" on a guess', () => {
  it('a finished transcript that names a message delivers it', () => {
    queue('sent')
    settleOutbox(C, new Set(['sent']))
    expect(outbox()).toEqual([])
  })

  it('a finished transcript that does not name it means it never arrived (live report 2026-11-18)', () => {
    queue('lost')
    settleOutbox(C, new Set())
    expect(outbox()).toEqual([['lost', 'failed']])
  })

  it('a send still in flight is not failed by a read that raced its 202', () => {
    queue('racing')
    noteSending('racing', payload)
    settleOutbox(C, new Set())
    expect(outbox()).toEqual([['racing', 'queued']])
    noteSent('racing')
    settleOutbox(C, new Set())
    expect(outbox()).toEqual([['racing', 'failed']])
  })

  it('a failed send says so at once', () => {
    queue('dropped')
    noteSending('dropped', payload)
    failQueued(C, 'dropped')
    expect(outbox()).toEqual([['dropped', 'failed']])
  })
})

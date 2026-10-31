import { describe, it, expect } from 'vitest'
import { NEW_CHAT, entryFor, moveFor, openFor, readEntry } from './chatHistory'

const known = (ids: string[]) => (id: string) => ids.includes(id)

describe('chat history rules', () => {
  it('puts a written chat in the Lucius address and a new one only in history state', () => {
    expect(entryFor('/lucius', 'a', true)).toEqual({ conversation: 'a', chat: 'a' })
    expect(entryFor('/lucius', 'draft', false)).toEqual({ conversation: undefined, chat: NEW_CHAT })
    expect(entryFor('/dashboard', 'a', true)).toEqual({ conversation: undefined, chat: 'a' })
  })

  it('reads ?conversation= only on the Lucius page', () => {
    expect(readEntry('/lucius', 'a', { luciusChat: 'a' })).toEqual({ conversation: 'a', chat: 'a' })
    expect(readEntry('/dashboard', 'a', null)).toEqual({ conversation: undefined, chat: undefined })
  })

  it('gives a switch the person made its own entry, and rewrites in place otherwise', () => {
    const current = { conversation: 'a', chat: 'a' }
    const desired = { conversation: 'b', chat: 'b' }
    expect(moveFor({ pathname: '/lucius', current, desired, switched: true })).toBe('push')
    expect(moveFor({ pathname: '/lucius', current, desired, switched: false })).toBe('replace')
    expect(moveFor({ pathname: '/lucius', current: desired, desired, switched: true })).toBeNull()
  })

  it('the first message gives a new chat its address without adding an entry', () => {
    const current = { conversation: undefined, chat: NEW_CHAT }
    const desired = entryFor('/lucius', 'n1', true)
    expect(moveFor({ pathname: '/lucius', current, desired, switched: false })).toBe('replace')
  })

  it('leaves a page it never wrote alone unless the person switched', () => {
    const untouched = { conversation: undefined, chat: undefined }
    const desired = { conversation: undefined, chat: 'b' }
    expect(
      moveFor({ pathname: '/dashboard', current: untouched, desired, switched: false }),
    ).toBeNull()
    expect(moveFor({ pathname: '/dashboard', current: untouched, desired, switched: true })).toBe(
      'push',
    )
    const stamped = { conversation: undefined, chat: NEW_CHAT }
    expect(moveFor({ pathname: '/dashboard', current: stamped, desired, switched: false })).toBe(
      'replace',
    )
  })

  it('Back opens the chat the entry stands for, while it still exists', () => {
    const entry = { conversation: undefined, chat: 'a' }
    expect(
      openFor({ entry, activeId: 'b', activePersisted: true, known: known(['a', 'b']) }),
    ).toEqual({ kind: 'switch', id: 'a' })
    expect(openFor({ entry, activeId: 'b', activePersisted: true, known: known(['b']) })).toBeNull()
    expect(openFor({ entry, activeId: 'a', activePersisted: true, known: known(['a']) })).toBeNull()
  })

  it('a typed or opened link opens its chat even before this browser has listed it', () => {
    const entry = { conversation: 'old', chat: undefined }
    expect(openFor({ entry, activeId: 'b', activePersisted: true, known: known(['b']) })).toEqual({
      kind: 'switch',
      id: 'old',
    })
  })

  it('Back to a new-chat entry starts a new chat, unless one is already open', () => {
    const entry = { conversation: undefined, chat: NEW_CHAT }
    expect(openFor({ entry, activeId: 'a', activePersisted: true, known: known(['a']) })).toEqual({
      kind: 'fresh',
    })
    expect(openFor({ entry, activeId: 'd', activePersisted: false, known: known([]) })).toBeNull()
  })
})

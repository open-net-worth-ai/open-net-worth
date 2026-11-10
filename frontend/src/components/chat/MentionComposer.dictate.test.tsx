// @vitest-environment jsdom
//
// Dictation (owner, 2026-11-03): the phrase being spoken shows in the box and is revised in place
// until the browser calls it final; the next phrase starts after it, and typed text is untouched.

import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, createRef } from 'react'
import { cleanup, render } from '@testing-library/react'
import { MentionComposer } from './MentionComposer'
import type { MentionComposerHandle } from './MentionComposer'

afterEach(cleanup)

function renderComposer() {
  const ref = createRef<MentionComposerHandle>()
  const onContentChange = vi.fn()
  const { container } = render(
    <MentionComposer
      ref={ref}
      onSubmit={vi.fn()}
      onTrigger={vi.fn()}
      onContentChange={onContentChange}
    />,
  )
  const editor = container.querySelector<HTMLElement>('.chat-composer')!
  return { ref, editor, onContentChange }
}

describe('dictate', () => {
  it('revises the phrase in place, then starts the next one after it', () => {
    const { ref, editor, onContentChange } = renderComposer()
    act(() => ref.current!.appendText('Note:', { focus: false }))
    act(() => ref.current!.dictate('what did I', false))
    expect(editor.textContent).toBe('Note: what did I')
    act(() => ref.current!.dictate('what did I spend', false))
    act(() => ref.current!.dictate('what did I spend on coffee', true))
    expect(editor.textContent).toBe('Note: what did I spend on coffee')
    act(() => ref.current!.dictate('this month', true))
    expect(editor.textContent).toBe('Note: what did I spend on coffee this month')
    expect(onContentChange).toHaveBeenLastCalledWith(false)
  })

  it('never moves focus into the box (on a phone that hides the tab bar)', () => {
    const { ref, editor } = renderComposer()
    act(() => ref.current!.dictate('what did I spend', false))
    expect(document.activeElement).not.toBe(editor)
    expect(editor.contains(document.activeElement)).toBe(false)
  })

  it('a phrase revised to nothing leaves no stray space', () => {
    const { ref, editor } = renderComposer()
    act(() => ref.current!.appendText('Hi', { focus: false }))
    act(() => ref.current!.dictate('uh', false))
    act(() => ref.current!.dictate('', true))
    expect(editor.textContent).toBe('Hi')
  })
})

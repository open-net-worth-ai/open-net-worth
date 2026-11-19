// @vitest-environment jsdom
//
// The tag picker: search the tags already in use, select one, or create a new one — the same
// shape as the category picker. Used by the drawer, the register's Tags cell and the add row.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { TagField } from './TagField'
import { tagItems } from './tagOptions'

vi.mock('../../../api/client', () => ({
  authFetch: vi.fn(async () => ({
    ok: true,
    json: async () => ({
      tags: [
        { tag: 'reimbursable', count: 5 },
        { tag: 'japan-trip', count: 2 },
        { tag: 'gift', count: 1 },
      ],
    }),
  })),
}))

afterEach(cleanup)
beforeEach(() => localStorage.clear())

function Harness({
  initial = [] as string[],
  onLeave,
}: {
  initial?: string[]
  onLeave?: (t: string[]) => void
}) {
  const [tags, setTags] = useState(initial)
  return (
    <>
      <TagField value={tags} onChange={setTags} onLeave={onLeave} />
      <output data-testid="tags">{tags.join('|')}</output>
      <button>elsewhere</button>
    </>
  )
}

const input = () => screen.getByLabelText('Tags', { selector: 'input' })
const value = () => screen.getByTestId('tags').textContent

describe('tagItems', () => {
  it('filters known tags, skips chosen ones, and offers Create for a new name', () => {
    const known = ['reimbursable', 'japan-trip', 'gift']
    expect(tagItems(known, ['gift'], '')).toEqual([
      { type: 'tag', tag: 'reimbursable' },
      { type: 'tag', tag: 'japan-trip' },
    ])
    // A partial match still offers creating exactly what was typed.
    expect(tagItems(known, [], 'Japan')).toEqual([
      { type: 'tag', tag: 'japan-trip' },
      { type: 'create', tag: 'japan' },
    ])
    expect(tagItems(known, [], 'Work Trip')).toEqual([{ type: 'create', tag: 'work-trip' }])
    expect(tagItems(known, ['work-trip'], 'work trip')).toEqual([])
  })
})

describe('TagField', () => {
  it('lists existing tags, most used first, and selects one on click', async () => {
    render(<Harness />)
    fireEvent.focus(input())
    const options = await screen.findAllByRole('option')
    expect(options.map((o) => o.textContent)).toEqual(['reimbursable', 'japan-trip', 'gift'])
    fireEvent.mouseDown(screen.getByRole('option', { name: 'japan-trip' }))
    expect(value()).toBe('japan-trip')
    expect(screen.queryByRole('option', { name: 'japan-trip' })).toBeNull()
  })

  it('creates a new tag from the typed name with Enter', async () => {
    render(<Harness />)
    fireEvent.focus(input())
    fireEvent.change(input(), { target: { value: 'Work Trip' } })
    expect(await screen.findByRole('option', { name: /Create "work-trip"/ })).toBeTruthy()
    fireEvent.keyDown(input(), { key: 'Enter' })
    expect(value()).toBe('work-trip')
  })

  it('Enter picks the highlighted match, Backspace on empty removes the last chip', async () => {
    render(<Harness initial={['gift']} />)
    fireEvent.focus(input())
    fireEvent.change(input(), { target: { value: 'reim' } })
    await screen.findByRole('option', { name: 'reimbursable' })
    fireEvent.keyDown(input(), { key: 'Enter' })
    expect(value()).toBe('gift|reimbursable')
    fireEvent.keyDown(input(), { key: 'Backspace' })
    expect(value()).toBe('gift')
  })

  it('removes a chip with its × button', () => {
    render(<Harness initial={['gift', 'japan-trip']} />)
    fireEvent.click(screen.getByLabelText('Remove tag gift'))
    expect(value()).toBe('japan-trip')
  })

  it('leaving the field keeps a half-typed tag and reports the final list', async () => {
    const onLeave = vi.fn()
    render(<Harness onLeave={onLeave} />)
    fireEvent.focus(input())
    fireEvent.change(input(), { target: { value: 'new one' } })
    fireEvent.blur(input(), { relatedTarget: screen.getByText('elsewhere') })
    await waitFor(() => expect(value()).toBe('new-one'))
    expect(onLeave).toHaveBeenCalledWith(['new-one'])
  })
})

describe('TagField ←/→ between existing tags', () => {
  const picked = () =>
    Array.from(document.querySelectorAll('.acct-tagf-chip.sel')).map((c) => c.textContent)

  it('← from the start of the input walks left through the chips, → walks back to the input', () => {
    render(<Harness initial={['gift', 'japan-trip', 'work']} />)
    input().focus()
    fireEvent.keyDown(input(), { key: 'ArrowLeft' })
    expect(picked()).toEqual(['work'])
    fireEvent.keyDown(input(), { key: 'ArrowLeft' })
    fireEvent.keyDown(input(), { key: 'ArrowLeft' })
    fireEvent.keyDown(input(), { key: 'ArrowLeft' }) // stops at the first
    expect(picked()).toEqual(['gift'])
    fireEvent.keyDown(input(), { key: 'ArrowRight' })
    expect(picked()).toEqual(['japan-trip'])
    fireEvent.keyDown(input(), { key: 'ArrowRight' })
    fireEvent.keyDown(input(), { key: 'ArrowRight' })
    expect(picked()).toEqual([]) // back in the input
    expect(document.activeElement).toBe(input())
  })

  it('Backspace / Delete remove the picked chip, not the last one', () => {
    render(<Harness initial={['gift', 'japan-trip', 'work']} />)
    fireEvent.focus(input())
    fireEvent.keyDown(input(), { key: 'ArrowLeft' })
    fireEvent.keyDown(input(), { key: 'ArrowLeft' })
    fireEvent.keyDown(input(), { key: 'Backspace' })
    expect(value()).toBe('gift|work')
    expect(picked()).toEqual(['gift'])
    fireEvent.keyDown(input(), { key: 'Delete' })
    expect(value()).toBe('work')
    expect(picked()).toEqual(['work'])
  })

  it('typing drops the pick and goes back to searching', () => {
    render(<Harness initial={['gift']} />)
    fireEvent.focus(input())
    fireEvent.keyDown(input(), { key: 'ArrowLeft' })
    expect(picked()).toEqual(['gift'])
    fireEvent.change(input(), { target: { value: 'w' } })
    expect(picked()).toEqual([])
  })
})

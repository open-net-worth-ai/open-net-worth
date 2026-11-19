// @vitest-environment jsdom
//
// Focusing the empty register search box offers Uncategorized / Uncleared / Needs review
// as one-click filters — without a stray Enter adding one, and without re-offering a
// filter that is already on.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, fireEvent, cleanup, screen } from '@testing-library/react'
import { SearchFilter, type FilterToken } from './SearchFilter'

const suggest = { categories: [], payees: [], accounts: [], tags: [] }

function setup(tokens: FilterToken[] = []) {
  const addToken = vi.fn()
  render(
    <SearchFilter
      tokens={tokens}
      addToken={addToken}
      removeToken={() => {}}
      clearTokens={() => {}}
      suggest={suggest}
      sym="$"
    />,
  )
  const input = screen.getByRole('textbox')
  fireEvent.focus(input)
  return { addToken, input }
}

const options = () => screen.queryAllByRole('button').map((b) => b.textContent)

afterEach(cleanup)

describe('SearchFilter — quick picks', () => {
  it('lists Uncategorized, Uncleared and Needs review on focus, and one click adds it', () => {
    const { addToken } = setup()
    expect(options()).toEqual(['CategoryUncategorized', 'StatusUncleared', 'StatusNeeds review'])
    fireEvent.mouseDown(screen.getByText('Uncategorized'))
    expect(addToken).toHaveBeenCalledWith(
      expect.objectContaining({ field: 'uncategorized', value: true, label: 'Uncategorized' }),
    )
  })

  it('Enter on the empty box adds nothing until the user arrows to a pick', () => {
    const { addToken, input } = setup()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(addToken).not.toHaveBeenCalled()
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(addToken).toHaveBeenCalledWith(expect.objectContaining({ field: 'uncategorized' }))
  })

  it('does not re-offer a filter that is already on', () => {
    setup([{ field: 'cleared', value: false, label: 'Uncleared' }])
    expect(screen.queryByText('Uncleared', { selector: '.acct-sug-txt' })).toBeNull()
    expect(screen.getByText('Uncategorized')).toBeTruthy()
  })

  it('closes the list once a pick is made; a click or ↓ on the box brings it back', () => {
    const { addToken, input } = setup()
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(addToken).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('listbox')).toBeNull()
    fireEvent.click(input)
    expect(screen.getByRole('listbox')).toBeTruthy()
    fireEvent.mouseDown(screen.getByText('Uncleared'))
    expect(screen.queryByRole('listbox')).toBeNull()
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(screen.getByRole('listbox')).toBeTruthy()
  })
})

// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render } from '@testing-library/react'
import { keyActivate } from './keyActivate'

describe('keyActivate', () => {
  it('activates on click, Enter and Space, not on nested keys', () => {
    const fn = vi.fn()
    const { getByTestId } = render(
      <div data-testid="row" {...keyActivate(fn)}>
        <input data-testid="inner" />
      </div>,
    )
    const row = getByTestId('row')
    expect(row.tabIndex).toBe(0)
    fireEvent.click(row)
    fireEvent.keyDown(row, { key: 'Enter' })
    fireEvent.keyDown(row, { key: ' ' })
    fireEvent.keyDown(getByTestId('inner'), { key: 'Enter' })
    expect(fn).toHaveBeenCalledTimes(3)
  })
})

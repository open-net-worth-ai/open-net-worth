// @vitest-environment jsdom
import { render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useKeepFieldInView } from './useKeepFieldInView'

let resize: (() => void) | null = null
class FakeResizeObserver {
  constructor(cb: () => void) {
    resize = cb
  }
  observe() {}
  disconnect() {
    resize = null
  }
}

function Stage() {
  const ref = useKeepFieldInView<HTMLDivElement>()
  return (
    <div ref={ref}>
      <input aria-label="inside" />
      <button type="button">chip</button>
    </div>
  )
}

describe('useKeepFieldInView', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    resize = null
  })

  it('scrolls the focused field back into view when the container is resized', () => {
    vi.stubGlobal('ResizeObserver', FakeResizeObserver)
    const { getByLabelText, getByText } = render(<Stage />)
    const input = getByLabelText('inside')
    const button = getByText('chip')
    input.scrollIntoView = vi.fn()
    button.scrollIntoView = vi.fn()

    // The keyboard shrinks the stage after the field has focus.
    input.focus()
    resize?.()
    expect(input.scrollIntoView).toHaveBeenCalledWith({ block: 'start' })

    // A resize with a chip focused (no keyboard) moves nothing.
    button.focus()
    resize?.()
    expect(button.scrollIntoView).not.toHaveBeenCalled()
  })

  it('stops observing when the container goes away', () => {
    vi.stubGlobal('ResizeObserver', FakeResizeObserver)
    const { unmount } = render(<Stage />)
    expect(resize).not.toBeNull()
    unmount()
    expect(resize).toBeNull()
  })
})

// @vitest-environment jsdom

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { FormError } from './FormError'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('FormError', () => {
  it('renders nothing without a message', () => {
    const { container } = render(<FormError message={null} />)
    expect(container.firstChild).toBeNull()
  })

  // A refused Save at the foot of a scrolled modal looked like nothing happened (UAT DS-DUP-REBOOK).
  it('announces the message and scrolls it into view when it appears', () => {
    const scrolled = vi.fn()
    Element.prototype.scrollIntoView = scrolled
    const { rerender, getByRole } = render(<FormError message={null} />)
    expect(scrolled).not.toHaveBeenCalled()

    rerender(<FormError message="This is already in your ledger" />)

    expect(getByRole('alert').textContent).toBe('This is already in your ledger')
    expect(scrolled).toHaveBeenCalledWith({ block: 'nearest' })
  })
})

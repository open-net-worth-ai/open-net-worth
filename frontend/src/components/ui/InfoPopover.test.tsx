// @vitest-environment jsdom
//
// Regression test for the "i" bubble getting stuck open on phones. iOS Safari replays a
// synthetic mouseenter/mouseleave sequence after a tap, but only a real mouse produces the
// matching mouseleave — a tap never does. InfoPopover used to listen on mouseenter/mouseleave
// directly, so a tap latched `revealed` true forever and the bubble never closed even after the
// scrim tap cleared `pinned`. Pointer events carry a real `pointerType`, so gating on
// `pointerType === 'mouse'` is what actually distinguishes a tap from a hover.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, fireEvent, cleanup, screen } from '@testing-library/react'
import { InfoPopover } from './InfoPopover'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('InfoPopover — touch vs. mouse', () => {
  it('an outside tap fully closes a focused, pinned bubble', () => {
    render(<InfoPopover text="Some info" />)
    const button = screen.getByRole('button', { name: 'More information' })

    fireEvent.focus(button)
    fireEvent.click(button)
    expect(screen.getByRole('tooltip')).toBeTruthy()

    const scrim = document.querySelector('.info-pop-scrim') as HTMLElement
    // Account editor sheets intentionally own touch gestures (`touch-action: none`), which can
    // suppress the synthetic click that normally follows a touch. The dismiss layer must close
    // on the actual intercepted pointer event instead.
    fireEvent.pointerDown(scrim, { pointerType: 'touch' })
    expect(screen.queryByRole('tooltip')).toBeNull()
  })

  it('Escape closes a pinned bubble', () => {
    render(<InfoPopover text="Some info" />)
    const button = screen.getByRole('button', { name: 'More information' })

    fireEvent.click(button)
    expect(screen.getByRole('tooltip')).toBeTruthy()
    fireEvent.keyDown(button, { key: 'Escape' })
    expect(screen.queryByRole('tooltip')).toBeNull()
  })

  it('a real mouse still reveals on hover and hides on leave, no click required', () => {
    render(<InfoPopover text="Some info" />)
    const wrap = document.querySelector('.info-pop-wrap') as HTMLElement

    fireEvent.pointerEnter(wrap, { pointerType: 'mouse' })
    expect(screen.getByRole('tooltip')).toBeTruthy()

    fireEvent.pointerLeave(wrap, { pointerType: 'mouse' })
    expect(screen.queryByRole('tooltip')).toBeNull()
  })
})

// Owner report 2026-10-26: on a phone, tapping the "i" under Cleared showed nothing. The bubble
// opened, but inside the balance figures' scrolling row (`overflow-x: auto`), which clipped it.
describe('InfoPopover — the bubble escapes any clipping ancestor', () => {
  function triggerAt(rect: Partial<DOMRect>) {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      top: 0,
      bottom: 0,
      left: 0,
      right: 0,
      width: 16,
      height: 16,
      x: 0,
      y: 0,
      toJSON: () => ({}),
      ...rect,
    } as DOMRect)
  }

  it('renders on <body>, fixed beside its trigger — outside an overflow container', () => {
    triggerAt({ top: 100, bottom: 116, left: 40 })
    render(
      <div style={{ overflowX: 'auto' }} data-testid="scroller">
        <InfoPopover text="Posted" width={200} />
      </div>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'More information' }))

    const tip = screen.getByRole('tooltip')
    expect(screen.getByTestId('scroller').contains(tip)).toBe(false)
    expect(tip.parentElement).toBe(document.body)
    expect(tip.style.top).toBe('120px')
    expect(tip.style.left).toBe('40px')
  })

  it('stays inside a phone-width screen when its trigger sits near the right edge', () => {
    window.innerWidth = 375
    triggerAt({ top: 100, bottom: 116, left: 330 })
    render(<InfoPopover text="Posted" width={250} />)
    fireEvent.click(screen.getByRole('button', { name: 'More information' }))

    const tip = screen.getByRole('tooltip')
    expect(parseFloat(tip.style.left) + parseFloat(tip.style.width)).toBeLessThanOrEqual(375 - 8)
  })

  it('closes on scroll, which would otherwise leave it behind', () => {
    render(<InfoPopover text="Posted" />)
    fireEvent.click(screen.getByRole('button', { name: 'More information' }))
    expect(screen.getByRole('tooltip')).toBeTruthy()

    fireEvent.scroll(window)
    expect(screen.queryByRole('tooltip')).toBeNull()
  })
})

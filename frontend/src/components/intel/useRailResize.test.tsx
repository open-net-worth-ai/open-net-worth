// @vitest-environment jsdom

import { cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { RAIL_SHARE_KEY, useRailResize } from './useRailResize'

function Harness() {
  const { bodyRef, railRef, style, handleProps } = useRailResize()
  return (
    <div data-testid="body" ref={bodyRef} style={style}>
      <div data-testid="rail" ref={railRef}>
        <div data-testid="handle" {...handleProps} />
      </div>
    </div>
  )
}

function mount() {
  const view = render(<Harness />)
  const body = view.getByTestId('body')
  const rail = view.getByTestId('rail')
  const handle = view.getByTestId('handle')
  Object.defineProperty(body, 'clientWidth', { configurable: true, value: 1000 })
  rail.getBoundingClientRect = () => ({ width: 300 }) as DOMRect
  return { body, handle }
}

function dragBy(handle: HTMLElement, dx: number) {
  fireEvent.pointerDown(handle, { pointerId: 1, isPrimary: true, button: 0, clientX: 500 })
  fireEvent.pointerMove(handle, { pointerId: 1, clientX: 500 + dx })
  fireEvent.pointerUp(handle, { pointerId: 1, clientX: 500 + dx })
}

beforeEach(() => localStorage.clear())
afterEach(cleanup)

describe('useRailResize', () => {
  it('leaves the automatic width alone until dragged', () => {
    const { body } = mount()
    expect(body.style.getPropertyValue('--asd-rail-w')).toBe('')
  })

  it('dragging the left edge left widens the rail and remembers the share', () => {
    const { body, handle } = mount()
    dragBy(handle, -100)
    expect(body.style.getPropertyValue('--asd-rail-w')).toBe('clamp(240px, 40.00%, 560px)')
    expect(localStorage.getItem(RAIL_SHARE_KEY)).toBe('0.4')
  })

  it('never takes more than half the body or less than the floor', () => {
    const { body, handle } = mount()
    dragBy(handle, -900)
    expect(body.style.getPropertyValue('--asd-rail-w')).toBe('clamp(240px, 50.00%, 560px)')
    dragBy(handle, 900)
    expect(body.style.getPropertyValue('--asd-rail-w')).toBe('clamp(240px, 24.00%, 560px)')
  })

  it('restores a saved share and double-click resets to automatic', () => {
    localStorage.setItem(RAIL_SHARE_KEY, '0.35')
    const { body, handle } = mount()
    expect(body.style.getPropertyValue('--asd-rail-w')).toBe('clamp(240px, 35.00%, 560px)')
    fireEvent.doubleClick(handle)
    expect(body.style.getPropertyValue('--asd-rail-w')).toBe('')
    expect(localStorage.getItem(RAIL_SHARE_KEY)).toBeNull()
  })
})

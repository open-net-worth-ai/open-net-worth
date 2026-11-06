// @vitest-environment jsdom
import { useRef } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useSwipeToMinimize } from './useSwipeToMinimize'

function Screen({ onMinimize, enabled = true }: { onMinimize: () => void; enabled?: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  useSwipeToMinimize(ref, onMinimize, enabled)
  return (
    <div ref={ref} data-testid="screen">
      <div className="lx-callsettings" data-testid="sheet" />
    </div>
  )
}

function swipe(el: Element, from: [number, number], to: [number, number]) {
  const at = ([clientX, clientY]: [number, number]) => ({ touches: [{ clientX, clientY }] })
  fireEvent.touchStart(el, at(from))
  fireEvent.touchMove(el, at(to))
  fireEvent.touchEnd(el, { touches: [] })
}

afterEach(cleanup)

describe('swiping the call screen', () => {
  it('folds it down on a long pull', async () => {
    const onMinimize = vi.fn()
    render(<Screen onMinimize={onMinimize} />)
    swipe(screen.getByTestId('screen'), [100, 100], [100, 400])
    await waitFor(() => expect(onMinimize).toHaveBeenCalledOnce())
  })

  it('leaves it where it is for a sideways swipe, or a swipe up', () => {
    const onMinimize = vi.fn()
    render(<Screen onMinimize={onMinimize} />)
    const el = screen.getByTestId('screen')
    swipe(el, [100, 100], [350, 130])
    swipe(el, [100, 400], [100, 100])
    expect(onMinimize).not.toHaveBeenCalled()
    expect(el.style.transform).toBe('')
  })

  it('leaves the settings sheet to itself', () => {
    const onMinimize = vi.fn()
    render(<Screen onMinimize={onMinimize} />)
    swipe(screen.getByTestId('sheet'), [100, 100], [100, 400])
    expect(onMinimize).not.toHaveBeenCalled()
  })

  it('does nothing once the call has ended', () => {
    const onMinimize = vi.fn()
    render(<Screen onMinimize={onMinimize} enabled={false} />)
    swipe(screen.getByTestId('screen'), [100, 100], [100, 400])
    expect(onMinimize).not.toHaveBeenCalled()
  })
})

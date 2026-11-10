// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { SwipeActions } from './SwipeActions'

const original = Object.getOwnPropertyDescriptor(window, 'matchMedia')

function setTouch(touch: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: ((query: string) => ({
      matches: touch && query === '(hover: none) and (pointer: coarse)',
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as typeof window.matchMedia,
  })
}

function Row({
  name,
  onEdit = vi.fn(),
  onDelete = vi.fn(),
  onTap = vi.fn(),
}: {
  name: string
  onEdit?: () => void
  onDelete?: () => void
  onTap?: () => void
}) {
  return (
    <SwipeActions
      actions={[
        { label: 'Edit', ariaLabel: 'Edit ' + name, onClick: onEdit },
        { label: 'Delete', ariaLabel: 'Delete ' + name, onClick: onDelete, tone: 'danger' },
      ]}
    >
      <button type="button" onClick={onTap}>
        {name}
      </button>
    </SwipeActions>
  )
}

const rowOf = (name: string) => screen.getByText(name).closest('.ui-swipe') as HTMLElement
const faceOf = (name: string) => rowOf(name).querySelector('.ui-swipe-face') as HTMLElement
const isOpen = (name: string) => rowOf(name).hasAttribute('data-open')

/** A finger drag across a row's face: down, a few moves `ms` apart, up. */
function drag(name: string, dx: number, dy = 0, ms = 100) {
  const face = faceOf(name)
  const at = (f: number) => ({ pointerId: 1, clientX: 200 + dx * f, clientY: 100 + dy * f })
  fireEvent.pointerDown(face, at(0))
  for (const f of [0.25, 0.5, 1]) {
    vi.advanceTimersByTime(ms)
    fireEvent.pointerMove(face, at(f))
  }
  fireEvent.pointerUp(face, at(1))
  fireEvent.click(face)
}

beforeEach(() => {
  vi.useFakeTimers()
  setTouch(true)
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  if (original) Object.defineProperty(window, 'matchMedia', original)
})

describe('SwipeActions', () => {
  it('a drag left past the threshold opens the row onto its actions', () => {
    const onTap = vi.fn()
    render(<Row name="Housing" onTap={onTap} />)
    drag('Housing', -100)
    expect(isOpen('Housing')).toBe(true)
    expect(rowOf('Housing').style.getPropertyValue('--ui-swipe-x')).toBe('-144px')
    expect(screen.getByRole('button', { name: 'Edit Housing' }).textContent).toBe('Edit')
    expect(onTap).not.toHaveBeenCalled()
  })

  it('a short drag snaps back closed', () => {
    render(<Row name="Housing" />)
    drag('Housing', -30)
    expect(isOpen('Housing')).toBe(false)
    expect(rowOf('Housing').style.getPropertyValue('--ui-swipe-x')).toBe('0px')
  })

  it('a quick flick opens it under the threshold', () => {
    render(<Row name="Housing" />)
    drag('Housing', -40, 0, 5)
    expect(isOpen('Housing')).toBe(true)
  })

  it('a vertical drag is a scroll and never opens it', () => {
    render(<Row name="Housing" />)
    drag('Housing', -10, 120)
    expect(isOpen('Housing')).toBe(false)
  })

  it('keeps its moves from the page gestures above it once it is a swipe', () => {
    const onParentMove = vi.fn()
    render(
      <div onPointerMove={onParentMove}>
        <Row name="Housing" />
      </div>,
    )
    drag('Housing', -100)
    expect(onParentMove).not.toHaveBeenCalled()
  })

  it('leaves a rightward swipe on a closed row to the page', () => {
    const onParentMove = vi.fn()
    render(
      <div onPointerMove={onParentMove}>
        <Row name="Housing" />
      </div>,
    )
    drag('Housing', 100)
    expect(isOpen('Housing')).toBe(false)
    expect(onParentMove).toHaveBeenCalled()
  })

  it('opens one row at a time', () => {
    render(
      <>
        <Row name="Housing" />
        <Row name="Holiday" />
      </>,
    )
    drag('Housing', -100)
    drag('Holiday', -100)
    expect(isOpen('Holiday')).toBe(true)
    expect(isOpen('Housing')).toBe(false)
  })

  it('a tap on the open row closes it without tapping through; a tap on a closed row does', () => {
    const onTap = vi.fn()
    render(<Row name="Housing" onTap={onTap} />)
    drag('Housing', -100)
    fireEvent.click(screen.getByText('Housing'))
    expect(isOpen('Housing')).toBe(false)
    expect(onTap).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText('Housing'))
    expect(onTap).toHaveBeenCalledTimes(1)
  })

  it('a tap elsewhere closes it', () => {
    render(
      <>
        <Row name="Housing" />
        <p>elsewhere</p>
      </>,
    )
    drag('Housing', -100)
    fireEvent.pointerDown(screen.getByText('elsewhere'))
    expect(isOpen('Housing')).toBe(false)
  })

  it('an action calls its handler and closes the row', () => {
    const onDelete = vi.fn()
    render(<Row name="Housing" onDelete={onDelete} />)
    drag('Housing', -100)
    fireEvent.click(screen.getByRole('button', { name: 'Delete Housing' }))
    expect(onDelete).toHaveBeenCalledTimes(1)
    expect(isOpen('Housing')).toBe(false)
  })

  it('a keyboard user reaching an action opens the row', () => {
    render(<Row name="Housing" />)
    fireEvent.focus(screen.getByRole('button', { name: 'Edit Housing' }))
    expect(isOpen('Housing')).toBe(true)
  })

  it('renders only its children where there is a hover pointer', () => {
    setTouch(false)
    render(<Row name="Housing" />)
    expect(document.querySelector('.ui-swipe')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Edit Housing' })).toBeNull()
  })
})

// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { Tooltip } from './Tooltip'
import { isSidebarToggleKey, shortcutKeys } from '../../utils/shortcuts'

// jsdom doesn't evaluate :focus-visible; treat test focus as keyboard focus (the tooltip opens
// on keyboard focus only — a clicked button keeping focus must not re-open it).
const realMatches = Element.prototype.matches
function stubFocusVisible(focusVisible: boolean) {
  // Cast: `matches` has typed-selector overloads that a plain `(selector: string)` stub cannot satisfy.
  Element.prototype.matches = function (this: Element, selector: string) {
    return selector === ':focus-visible' ? focusVisible : realMatches.call(this, selector)
  } as typeof realMatches
}
beforeEach(() => {
  vi.useFakeTimers()
  stubFocusVisible(true)
})
afterEach(() => {
  Element.prototype.matches = realMatches
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const setPlatform = (platform: string) => vi.stubGlobal('navigator', { platform })

describe('Tooltip', () => {
  it('shows the label and Mac glyph after the hover delay, not before', () => {
    setPlatform('MacIntel')
    render(
      <Tooltip label="Show sidebar" shortcut={['Mod', 'B']}>
        <button>x</button>
      </Tooltip>,
    )
    fireEvent.mouseEnter(screen.getByRole('button'))
    act(() => void vi.advanceTimersByTime(500))
    expect(screen.queryByRole('tooltip')).toBeNull()
    act(() => void vi.advanceTimersByTime(200))
    expect(screen.getByRole('tooltip').textContent).toBe('Show sidebar⌘B')
  })

  it('uses Ctrl off the Mac', () => {
    setPlatform('Win32')
    render(
      <Tooltip label="Back" shortcut={['Mod', '[']}>
        <button>x</button>
      </Tooltip>,
    )
    fireEvent.focus(screen.getByRole('button'))
    expect(screen.getByRole('tooltip').textContent).toBe('BackCtrl[')
  })

  it('appears instantly on focus and hides on blur and Escape', () => {
    setPlatform('MacIntel')
    render(
      <Tooltip label="Forward">
        <button>x</button>
      </Tooltip>,
    )
    const button = screen.getByRole('button')
    fireEvent.focus(button)
    expect(screen.getByRole('tooltip')).toBeTruthy()
    fireEvent.blur(button)
    expect(screen.queryByRole('tooltip')).toBeNull()
    fireEvent.focus(button)
    fireEvent.keyDown(button, { key: 'Escape' })
    expect(screen.queryByRole('tooltip')).toBeNull()
  })

  it('shows the next tip at once when moving between adjacent buttons', () => {
    setPlatform('MacIntel')
    render(
      <>
        <Tooltip label="One">
          <button>1</button>
        </Tooltip>
        <Tooltip label="Two">
          <button>2</button>
        </Tooltip>
      </>,
    )
    const [one, two] = screen.getAllByRole('button')
    if (!one || !two) throw new Error('expected two buttons')
    fireEvent.mouseEnter(one)
    act(() => void vi.advanceTimersByTime(700))
    expect(screen.getByRole('tooltip').textContent).toBe('One')
    fireEvent.mouseLeave(one)
    fireEvent.mouseEnter(two)
    expect(screen.getByRole('tooltip').textContent).toBe('Two')
  })
})

describe('shortcuts', () => {
  it('maps Mod per platform', () => {
    setPlatform('MacIntel')
    expect(shortcutKeys(['Mod', 'B'])).toEqual(['⌘', 'B'])
    setPlatform('Linux x86_64')
    expect(shortcutKeys(['Mod', 'B'])).toEqual(['Ctrl', 'B'])
  })

  it('recognises Cmd/Ctrl+B only', () => {
    const key = (init: KeyboardEventInit) => new KeyboardEvent('keydown', init)
    expect(isSidebarToggleKey(key({ key: 'b', metaKey: true }))).toBe(true)
    expect(isSidebarToggleKey(key({ key: 'B', ctrlKey: true }))).toBe(true)
    expect(isSidebarToggleKey(key({ key: 'b' }))).toBe(false)
    expect(isSidebarToggleKey(key({ key: 'b', metaKey: true, shiftKey: true }))).toBe(false)
    expect(isSidebarToggleKey(key({ key: 'k', metaKey: true }))).toBe(false)
  })
})

describe('Tooltip pointer focus', () => {
  it('does not open when focus comes from a click (not :focus-visible)', () => {
    stubFocusVisible(false)
    render(
      <Tooltip label="Show sidebar">
        <button type="button">x</button>
      </Tooltip>,
    )
    fireEvent.focus(screen.getByRole('button'))
    act(() => vi.advanceTimersByTime(2000))
    expect(screen.queryByRole('tooltip')).toBeNull()
  })
})

describe('Tooltip onlyWhenClipped', () => {
  function cell(scrollWidth: number, clientWidth: number) {
    render(
      <Tooltip label="disputed, temporary-credit, email" onlyWhenClipped>
        <span data-testid="cell">disputed temporary-credit email</span>
      </Tooltip>,
    )
    const el = screen.getByTestId('cell')
    Object.defineProperty(el, 'scrollWidth', { configurable: true, value: scrollWidth })
    Object.defineProperty(el, 'clientWidth', { configurable: true, value: clientWidth })
    return el
  }

  it('shows the full text when the content is cut off', () => {
    fireEvent.mouseEnter(cell(240, 120))
    act(() => void vi.advanceTimersByTime(700))
    expect(screen.getByRole('tooltip').textContent).toBe('disputed, temporary-credit, email')
  })

  it('stays hidden when everything already fits', () => {
    fireEvent.mouseEnter(cell(120, 120))
    act(() => void vi.advanceTimersByTime(700))
    expect(screen.queryByRole('tooltip')).toBeNull()
  })
})

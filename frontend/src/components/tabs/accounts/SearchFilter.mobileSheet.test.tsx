// @vitest-environment jsdom
//
// Filters' mobile date-range sheet is one of the three .mb-sheet call sites — it never had its
// own drag gesture before useSheetDragToDismiss (only a decorative handle). This covers that
// it opens/closes via its existing "Done" mechanism and now drag-dismisses too.

import { describe, it, expect, afterEach } from 'vitest'
import { render, fireEvent, cleanup, screen } from '@testing-library/react'
import { Filters, type FilterState, type SuggestData } from './SearchFilter'
import type { ColumnVisibility } from './columns'

function mockViewport(matches: boolean) {
  const original = Object.getOwnPropertyDescriptor(window, 'matchMedia')
  const fn = (() => ({
    matches,
    media: '',
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
  Object.defineProperty(window, 'matchMedia', { value: fn, configurable: true, writable: true })
  return () => {
    if (original) Object.defineProperty(window, 'matchMedia', original)
  }
}

/** Every matchMedia query answers false: `useBreakpoint()` then falls through its min-width
 * ladder to 'sm' (below the smallest step), matching a real phone viewport, and
 * prefers-reduced-motion reads false too — so a committed drag's exit animation runs on its
 * real ~200ms timer, awaited explicitly below rather than assumed instant. */
const mockNarrowViewport = () => mockViewport(false)
/** Every matchMedia query answers true: `useBreakpoint()` resolves to its widest step ('xl'),
 * matching a real desktop viewport. */
const mockWideViewport = () => mockViewport(true)

const filter: FilterState = { preset: 'all', cfM: 1, cfY: 2024, ctM: 12, ctY: 2024, tokens: [] }
const suggest: SuggestData = { categories: [], payees: [], accounts: [], tags: [] }
const columnVis: ColumnVisibility = { hidden: new Set(), toggle: () => {}, reset: () => {} }

function renderFilters() {
  return render(
    <Filters
      filter={filter}
      suggest={suggest}
      sym="$"
      addToken={() => {}}
      removeToken={() => {}}
      clearTokens={() => {}}
      setPreset={() => {}}
      setCustom={() => {}}
      onAddTxn={() => {}}
      showAccount={false}
      columnVis={columnVis}
      showHidden={false}
      onToggleShowHidden={() => {}}
    />,
  )
}

afterEach(() => cleanup())

describe('Filters — mobile date-range sheet', () => {
  it('opens as a .mb-sheet and the Done button closes it', () => {
    const restore = mockNarrowViewport()
    try {
      renderFilters()
      fireEvent.click(screen.getByRole('button', { name: /Date range/ }))
      expect(document.querySelector('.mb-sheet.acct-viewsheet')).toBeTruthy()

      fireEvent.click(screen.getByText('Done'))
      expect(document.querySelector('.mb-sheet.acct-viewsheet')).toBeNull()
    } finally {
      restore()
    }
  })

  it('a downward drag on the handle past the distance threshold dismisses it', async () => {
    const restore = mockNarrowViewport()
    try {
      renderFilters()
      fireEvent.click(screen.getByRole('button', { name: /Date range/ }))
      const sheet = document.querySelector('.mb-sheet.acct-viewsheet') as HTMLElement
      const handle = sheet.querySelector('.mb-sheet-handle-wrap') as HTMLElement
      Object.defineProperty(sheet, 'offsetHeight', { value: 400, configurable: true })

      fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
      await new Promise((resolve) => setTimeout(resolve, 150))
      fireEvent.pointerMove(handle, { clientY: 400, pointerId: 1 })
      fireEvent.pointerUp(handle, { clientY: 400, pointerId: 1 })
      // No reduced-motion shortcut here (mockNarrowViewport reads it false) — wait out the
      // real ~200ms commit animation before the sheet's own onOpenChange(false) lands.
      await new Promise((resolve) => setTimeout(resolve, 250))

      expect(document.querySelector('.mb-sheet.acct-viewsheet')).toBeNull()
    } finally {
      restore()
    }
  })

  it('dragging the sheet body content directly (no scrollable overflow) drags the whole sheet, same as the handle', async () => {
    const restore = mockNarrowViewport()
    try {
      renderFilters()
      fireEvent.click(screen.getByRole('button', { name: /Date range/ }))
      const sheet = document.querySelector('.mb-sheet.acct-viewsheet') as HTMLElement
      Object.defineProperty(sheet, 'offsetHeight', { value: 400, configurable: true })

      fireEvent.pointerDown(sheet, { clientY: 200, pointerId: 1 })
      await new Promise((resolve) => setTimeout(resolve, 150))
      fireEvent.pointerMove(sheet, { clientY: 400, pointerId: 1 })
      fireEvent.pointerUp(sheet, { clientY: 400, pointerId: 1 })
      await new Promise((resolve) => setTimeout(resolve, 250))

      expect(document.querySelector('.mb-sheet.acct-viewsheet')).toBeNull()
    } finally {
      restore()
    }
  })

  it('dragging DOWN on scrolled body content is left to the browser — the sheet never moves and never dismisses', async () => {
    const restore = mockNarrowViewport()
    try {
      renderFilters()
      fireEvent.click(screen.getByRole('button', { name: /Date range/ }))
      // Filters' date-range sheet has no dedicated `-body` wrapper (unlike MobileTabBar/
      // TxnDetail) — its content renders straight into `.mb-sheet` itself, so the sheet root
      // doubles as the scrollable element here, same shape as TxnDetail's `.acct-detail`.
      const sheet = document.querySelector('.mb-sheet.acct-viewsheet') as HTMLElement
      Object.defineProperty(sheet, 'offsetHeight', { value: 400, configurable: true })
      Object.defineProperty(sheet, 'scrollHeight', { value: 600, configurable: true })
      Object.defineProperty(sheet, 'clientHeight', { value: 300, configurable: true })
      sheet.style.overflowY = 'auto'
      sheet.scrollTop = 150

      fireEvent.pointerDown(sheet, { clientY: 200, pointerId: 1 })
      await new Promise((resolve) => setTimeout(resolve, 150))
      fireEvent.pointerMove(sheet, { clientY: 500, pointerId: 1 }) // 300px pull, well past the 35% dismiss threshold
      fireEvent.pointerUp(sheet, { clientY: 500, pointerId: 1 })
      await new Promise((resolve) => setTimeout(resolve, 250))

      expect(sheet.scrollTop).toBe(150) // never touched — the hook never writes scrollTop
      expect(sheet.style.transform).toBe('') // never armed — declined at its first move
      expect(document.querySelector('.mb-sheet.acct-viewsheet')).toBeTruthy() // still open, never dismissed
    } finally {
      restore()
    }
  })

  it('desktop keeps the anchored popover, no .mb-sheet at all', () => {
    const restore = mockWideViewport()
    try {
      renderFilters()
      fireEvent.click(screen.getByRole('button', { name: /Date range/ }))
      expect(document.querySelector('.acct-viewpop')).toBeTruthy()
      expect(document.querySelector('.mb-sheet.acct-viewsheet')).toBeNull()
    } finally {
      restore()
    }
  })
})
